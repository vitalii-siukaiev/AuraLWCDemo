
import { LightningElement, track } from 'lwc';

export default class LwcChildEvent extends LightningElement {
    @track param = {
        firstName: 'John',
        lastName: 'Week',
        additional: {
            age: 23,
            gender: 'male'
        }
    };

    handleButtonClick() {
        const param = {...this.param};
        param.additional = {...this.param.additional};
        this.dispatchEvent(
            new CustomEvent('customevent', {
                detail: { param }
            })
        );
    }

}